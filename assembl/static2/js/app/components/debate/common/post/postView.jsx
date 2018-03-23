// @flow
import React from 'react';
import { Translate } from 'react-redux-i18n';

import { getDomElementOffset } from '../../../../utils/globalFunctions';
import Attachments from '../../../common/attachments';
import ProfileLine from '../../../common/profileLine';
import PostActions from '../../common/postActions';
import AnswerForm from '../../thread/answerForm';
import Nuggets from '../../thread/nuggets';
import RelatedIdeas from './relatedIdeas';
import PostBody from './postBody';
import HarvestingMenu from '../../../harvesting/harvestingMenu';
import type { Props as PostProps } from './index';

type Props = PostProps & {
  body: string,
  subject: string,
  handleEditClick: Function,
  modifiedSubject: React.Element<*>,
  isHarvesting: boolean
};

type State = {
  showAnswerForm: boolean,
  displayHarvestingAnchor: boolean
};

class PostView extends React.PureComponent<void, Props, State> {
  props: Props;

  state: State;

  answerTextarea: HTMLTextAreaElement;

  constructor(props: Props) {
    super(props);
    this.state = {
      showAnswerForm: false,
      displayHarvestingAnchor: false
    };
  }

  handleAnswerClick = () => {
    this.setState({ showAnswerForm: true }, this.props.measureTreeHeight);
    setTimeout(() => {
      if (!this.answerTextarea) return;
      const txtareaOffset = getDomElementOffset(this.answerTextarea).top;
      window.scrollTo({ top: txtareaOffset - this.answerTextarea.clientHeight, left: 0, behavior: 'smooth' });
    }, 200);
  };

  hideAnswerForm = () => {
    this.setState({ showAnswerForm: false }, this.props.measureTreeHeight);
  };

  recomputeTreeHeightOnImagesLoad = (el: HTMLElement) => {
    // recompute the tree height after images are loaded
    if (el) {
      const images = el.getElementsByTagName('img');
      Array.from(images).forEach(img =>
        img.addEventListener('load', () => {
          this.props.measureTreeHeight(400);
        })
      );
    }
  };

  handleMouseUpWhileHarvesting = (): void => {
    const { isHarvesting, translate } = this.props;
    if (isHarvesting && !translate) {
      this.setState({ displayHarvestingAnchor: true });
    }
  };

  render() {
    const {
      bodyMimeType,
      dbId,
      indirectIdeaContentLinks,
      creator,
      modificationDate,
      sentimentCounts,
      mySentiment,
      attachments,
      extracts
    } = this.props.data.post;
    const {
      borderLeftColor,
      handleEditClick,
      contentLocale,
      id,
      lang,
      ideaId,
      refetchIdea,
      // creationDate is retrieved by IdeaWithPosts query, not PostQuery
      creationDate,
      fullLevel,
      numChildren,
      routerParams,
      debateData,
      nuggetsManager,
      rowIndex,
      originalLocale,
      identifier,
      body,
      subject,
      modifiedSubject,
      multiColumns
    } = this.props;
    const translate = contentLocale !== originalLocale;

    const completeLevelArray = fullLevel ? [rowIndex, ...fullLevel.split('-').map(string => Number(string))] : [rowIndex];

    const answerTextareaRef = (el: HTMLTextAreaElement) => {
      this.answerTextarea = el;
    };

    const boxStyle = {
      borderLeftColor: borderLeftColor
    };

    let canReply = !multiColumns;
    // If we're in thread mode, check if the first idea associated to the post is multi columns.
    if (!multiColumns && indirectIdeaContentLinks && indirectIdeaContentLinks.length > 0) {
      canReply = indirectIdeaContentLinks[0].idea.messageViewOverride !== 'messageColumns';
    }

    const { displayHarvestingAnchor } = this.state;
    return (
      <div>
        {!multiColumns && (
          <Nuggets extracts={extracts} postId={id} nuggetsManager={nuggetsManager} completeLevel={completeLevelArray.join('-')} />
        )}
        {displayHarvestingAnchor && <HarvestingMenu />}
        <div className="box" style={boxStyle}>
          <div className="post-row">
            <div className="post-left" onMouseUp={this.handleMouseUpWhileHarvesting}>
              {creator && (
                <ProfileLine
                  userId={creator.userId}
                  userName={creator.displayName}
                  creationDate={creationDate}
                  locale={lang}
                  modified={modificationDate !== null}
                />
              )}
              <PostBody
                body={body}
                dbId={dbId}
                extracts={extracts}
                bodyMimeType={bodyMimeType}
                contentLocale={contentLocale}
                id={id}
                lang={lang}
                subject={modifiedSubject}
                originalLocale={originalLocale}
                translate={translate}
                translationEnabled={debateData.translationEnabled}
                bodyDivRef={this.recomputeTreeHeightOnImagesLoad}
              />

              <Attachments attachments={attachments} />

              {!multiColumns && (
                <div>
                  <RelatedIdeas indirectIdeaContentLinks={indirectIdeaContentLinks} />

                  <div className="answers annotation">
                    <Translate value="debate.thread.numberOfResponses" count={numChildren} />
                  </div>
                </div>
              )}
            </div>
            <div className="post-right">
              <PostActions
                creatorUserId={creator.userId}
                postId={id}
                handleEditClick={handleEditClick}
                sentimentCounts={sentimentCounts}
                mySentiment={mySentiment}
                numChildren={numChildren}
                routerParams={routerParams}
                debateData={debateData}
                postSubject={subject.replace('Re: ', '')}
                identifier={identifier}
              />
            </div>
          </div>
        </div>
        {canReply && (
          <div className={this.state.showAnswerForm ? 'answer-form' : 'collapsed-answer-form'}>
            <AnswerForm
              parentId={id}
              ideaId={ideaId}
              refetchIdea={refetchIdea}
              textareaRef={answerTextareaRef}
              hideAnswerForm={this.hideAnswerForm}
              handleAnswerClick={this.handleAnswerClick}
              identifier={identifier}
            />
          </div>
        )}
      </div>
    );
  }
}

export default PostView;